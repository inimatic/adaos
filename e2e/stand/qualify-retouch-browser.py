"""Run the trusted Retouch browser journey in fresh, owned provider-free stores."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

from dotenv import load_dotenv
import psutil
import requests

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--label", required=True)
parser.add_argument("--layouts", nargs="+", choices=["wide", "compact"], default=["wide", "compact"])
args = parser.parse_args()
repo = Path.cwd().resolve(); output = args.output.resolve()
if not output.is_relative_to(repo / "e2e/artifacts") or output == repo / "e2e/artifacts":
    parser.error("An explicit E2E artifact directory is required")
if not args.label or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in args.label):
    parser.error("A simple owned fixture label is required")
load_dotenv(repo / ".env")
token = os.getenv("ADAOS_E2E_HUB_TOKEN") or os.getenv("ADAOS_TOKEN")
if not token: parser.error("Local control token unavailable")
output.mkdir(parents=True, exist_ok=True)
results = []

def stop_owned(process):
    if process is None: return
    try:
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
        for child in reversed(children):
            if any("serve-retouch-fixture.py" in arg for arg in child.cmdline()): child.terminate()
        parent.terminate()
        process.wait(timeout=10)
    except (psutil.NoSuchProcess, ProcessLookupError): pass

for layout in args.layouts:
    owned = repo / ".tmp/retouch-browser-fixtures" / args.label / layout
    if owned.exists(): raise ValueError("Fixture already exists; use a fresh trial label")
    owned.mkdir(parents=True)
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0)); port = reserved.getsockname()[1]
    logs = []
    processes = [None]
    def start_fixture():
        stdout = (owned / "stdout.log").open("ab"); stderr = (owned / "stderr.log").open("ab")
        logs.extend([stdout, stderr])
        process = subprocess.Popen([sys.executable, "-X", "utf8", "e2e/stand/serve-retouch-fixture.py",
            "--owned-root", str(owned), "--source", str(args.source.resolve()), "--port", str(port)],
            cwd=repo, stdout=stdout, stderr=stderr,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        processes[0] = process
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if process.poll() is not None: raise RuntimeError("Owned fixture exited; inspect its private stderr.log")
            try:
                secret = (owned / "fixture-token").read_text(encoding="utf-8")
                session = requests.Session(); session.trust_env = False
                response = session.get(f"http://127.0.0.1:{port}/audit", headers={"X-Retouch-Fixture-Token": secret}, timeout=1)
                if response.ok: return
            except (OSError, requests.RequestException): pass
            time.sleep(.1)
        raise RuntimeError("Owned fixture startup timeout")

    stop = threading.Event(); restart_errors = []
    def restart_monitor():
        while not stop.wait(.1):
            marker = owned / "restart-requested.json"
            if not marker.exists(): continue
            try:
                checkpoint = json.loads(marker.read_text(encoding="utf-8"))
                stop_owned(processes[0]); start_fixture()
                (owned / "restart-complete.json").write_text(json.dumps({**checkpoint,
                    "same_owned_store": True, "restarted_at": time.time()}), encoding="utf-8")
            except Exception as exc: restart_errors.append(f"{type(exc).__name__}: {exc}")
            return

    monitor = None
    try:
        start_fixture()
        monitor = threading.Thread(target=restart_monitor, daemon=True); monitor.start()
        environment = os.environ.copy()
        environment.update(ADAOS_E2E_LAYOUT=layout, ADAOS_E2E_RETOUCH_FIXTURE=str(owned),
            ADAOS_E2E_OUTPUT=str(output / layout), ADAOS_E2E_HUB_TOKEN=token, ADAOS_E2E_RESTART_FIXTURE="1")
        with (owned / "browser-stdout.log").open("w", encoding="utf-8") as browser_out, (owned / "browser-stderr.log").open("w", encoding="utf-8") as browser_err:
            browser_process = subprocess.Popen(["node", "e2e/stand/browser/retouch-journey.mjs"], cwd=repo,
                env=environment, stdout=browser_out, stderr=browser_err, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            offset = 0; deadline = time.monotonic() + 900
            while browser_process.poll() is None:
                text = (owned / "browser-stdout.log").read_text(encoding="utf-8")
                if len(text) > offset: print(text[offset:], end="", flush=True); offset = len(text)
                if time.monotonic() > deadline:
                    browser_process.terminate(); raise RuntimeError("Retouch browser journey timeout")
                time.sleep(.25)
        text = (owned / "browser-stdout.log").read_text(encoding="utf-8")
        if len(text) > offset: print(text[offset:], end="", flush=True)
        errors = (owned / "browser-stderr.log").read_text(encoding="utf-8")
        if errors: print(errors[-1500:], file=sys.stderr, flush=True)
        result = {"layout": layout, "exit_code": browser_process.returncode, "restart_errors": restart_errors,
                  "fixture_root": str(owned), "report": str(output / layout / "journey.json")}
        if browser_process.returncode == 0:
            from adaos.e2e.retouch_verification import verify_downloads
            report_path = output / layout / "journey.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            try:
                report["independent_pixel_verification"] = verify_downloads(owned_root=owned, output=output / layout, report=report)
            except Exception as exc:
                report["passed"] = False
                report["independent_pixel_verification"] = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
                result["exit_code"] = 1
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        results.append(result)
    finally:
        stop.set()
        if monitor: monitor.join(timeout=50)
        stop_owned(processes[0])
        for handle in logs: handle.close()
    (output / "qualification.json").write_text(json.dumps({"schema": "adaos.e2e.retouch_qualification.v1",
        "scope": "owned synthetic fixture; provider quality and Core admission excluded", "results": results,
        "passed": all(row["exit_code"] == 0 and not row["restart_errors"] for row in results)}, indent=2), encoding="utf-8")
if not all(row["exit_code"] == 0 and not row["restart_errors"] for row in results): sys.exit(1)
