from __future__ import annotations

import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import shutil
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bootstrap_distribution", ROOT / "tools/bootstrap_distribution.py")
distribution = importlib.util.module_from_spec(spec)
spec.loader.exec_module(distribution)


@pytest.fixture
def tmp_path():
    parent = ROOT / '.tmp/bootstrap-distribution-tests'
    parent.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(dir=parent)).resolve()
    yield directory
    assert directory.is_relative_to(parent.resolve())
    shutil.rmtree(directory)


@pytest.fixture
def receiver():
    class Handler(BaseHTTPRequestHandler):
        requests = []
        durable = False

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.requests.append((self.path, payload))
            receipt = {"ok": True, "durable": self.durable, "attempt_id": payload["attempt_id"]}
            if "event_id" in payload:
                receipt["event_id"] = payload["event_id"]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(receipt).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield Handler, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join()


def test_outbox_requires_durable_receipt_and_replays_same_ids(tmp_path, receiver):
    handler, url = receiver
    attempt = tmp_path / "attempt-unit"
    attempt.mkdir()
    (attempt / "root-url").write_text(url)
    distribution.durable_json(attempt / "request.json", {"attempt_id": "unit", "profile": {"arch": "x86_64"}})
    reporter = distribution.Reporter(attempt)
    reporter.event("installation_failed", "uv_download_failed", 28)
    fact = json.loads(next(attempt.glob("event-*.json")).read_text())
    assert distribution.flush_outbox(tmp_path) == 1
    assert not list(attempt.glob("*.ack"))
    handler.durable = True
    assert distribution.flush_outbox(tmp_path) == 0
    assert len(list(attempt.glob("*.ack"))) == 2
    assert handler.requests[-1] == ("/v1/bootstrap/events", fact)
    count = len(handler.requests)
    assert distribution.flush_outbox(tmp_path) == 0
    assert len(handler.requests) == count


def test_runtime_lock_matches_shell_bootstrap_pins():
    lock = json.loads((ROOT / "tools/bootstrap-runtime.lock.json").read_text())
    shell = (ROOT / "tools/bootstrap-distribution.sh").read_text()
    for name in ("python", "uv"):
        assert lock[name]["url"] in shell
        assert lock[name]["sha256"] in shell


def test_outage_is_bounded_per_root_and_recovery_keeps_all_attempts(tmp_path, receiver):
    handler, url = receiver
    for index in range(12):
        attempt = tmp_path / f'attempt-{index:03d}'
        attempt.mkdir()
        (attempt / 'root-url').write_text(url)
        distribution.durable_json(attempt / 'request.json', {'attempt_id': f'case-{index:03d}'})
    assert distribution.flush_outbox(tmp_path) == 12
    assert len(handler.requests) == 1
    assert distribution.flush_outbox(tmp_path, budget_seconds=0) == 12
    assert len(handler.requests) == 1
    handler.durable = True
    assert distribution.flush_outbox(tmp_path) == 0
    assert len(list(tmp_path.glob('attempt-*/*.ack'))) == 12


def test_bundle_verification_precedes_any_environment_mutation(tmp_path):
    source = tmp_path / "kit/source"
    (source / "tools").mkdir(parents=True)
    lock = json.loads((ROOT / "tools/bootstrap-runtime.lock.json").read_text())
    (source / "tools/bootstrap-runtime.lock.json").write_text(json.dumps(lock))
    kit = source.parent
    selected_lock = distribution.runtime_lock(source, distribution.host_profile())
    (kit / "manifest.json").write_text(json.dumps({"schema": "adaos.bootstrap_bundle.v1", "runtime_lock": selected_lock,
                                                   "files": {"source/tools/bootstrap-runtime.lock.json": "0" * 64}}))
    import argparse
    attempt = tmp_path / "attempt-unit"
    attempt.mkdir()
    (attempt / "request.json").write_text('{"attempt_id":"unit"}')
    args = argparse.Namespace(dest=tmp_path / "install", kit=kit, source=source)
    with pytest.raises(ValueError, match="SHA256"):
        distribution.install(args, distribution.Reporter(attempt))
    assert not (args.dest / ".venv").exists()
    assert not (args.dest / ".adaos").exists()


def test_all_platform_pins_are_present_in_their_standalone_entrypoints():
    from bootstrap_runtime import PROFILES, runtime_lock
    for profile in PROFILES:
        lock = runtime_lock(ROOT, profile)
        script = 'bootstrap-distribution.ps1' if profile.startswith('windows') else 'bootstrap-distribution.sh'
        entrypoint = (ROOT / 'tools' / script).read_text(encoding='utf-8')
        for item in ('python', 'uv'):
            assert lock[item]['url'] in entrypoint
            assert lock[item]['sha256'] in entrypoint


def test_environment_activation_preserves_the_previous_environment_and_switches_only_the_pointer(tmp_path):
    first = tmp_path / '.adaos/environments/first'
    second = first.with_name('second')
    first.mkdir(parents=True); second.mkdir()
    (first / 'retained.txt').write_text('first environment remains intact')
    assert distribution.activate(tmp_path, first, 'first') is None
    assert (tmp_path / '.venv').resolve() == first.resolve()
    assert distribution.activate(tmp_path, second, 'second') == str(first.resolve())
    assert (tmp_path / '.venv').resolve() == second.resolve()
    assert (first / 'retained.txt').read_text() == 'first environment remains intact'


@pytest.mark.skipif(distribution.os.name != 'nt', reason='Windows junction recovery')
def test_interrupted_windows_pointer_activation_restores_the_retained_environment(tmp_path):
    previous = tmp_path / '.adaos/environments/previous'
    previous.mkdir(parents=True)
    backup = previous.with_name('active-before-crash')
    distribution.make_link(backup, previous)
    distribution.durable_json(tmp_path / '.adaos/venv-activation.json', {'backup': backup.relative_to(tmp_path).as_posix()})
    distribution.recover_activation(tmp_path)
    assert (tmp_path / '.venv').resolve() == previous.resolve()
    assert not backup.exists()


def test_unmanaged_environment_survives_install_attempt(tmp_path):
    (tmp_path / "install/.venv").mkdir(parents=True)
    import argparse
    args = argparse.Namespace(dest=tmp_path / "install")
    with pytest.raises(ValueError, match="unmanaged"):
        distribution.install(args, None)
    assert (args.dest / ".venv").exists()


def test_git_working_tree_is_preserved(tmp_path):
    (tmp_path / 'checkout/.git').mkdir(parents=True)
    user_source = tmp_path / 'checkout/user.py'
    user_source.write_text('uncommitted work')
    import argparse
    with pytest.raises(ValueError, match='Git checkout'):
        distribution.install(argparse.Namespace(dest=tmp_path / 'checkout'), None)
    assert user_source.read_text() == 'uncommitted work'
    assert not (tmp_path / 'checkout/.adaos').exists()
