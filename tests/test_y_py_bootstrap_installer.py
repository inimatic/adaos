from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import tomllib
import urllib.parse

from packaging.requirements import Requirement
import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def installer():
    spec = importlib.util.spec_from_file_location("y_py_installer", ROOT / "tools/install_patched_y_py.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def native_binary(monkeypatch, vector: bytes = bytes.fromhex("02010a0201")) -> None:
    monkeypatch.setitem(sys.modules, "y_py", SimpleNamespace(
        YDoc=object,
        apply_update=lambda document, update: None,
        encode_state_vector=lambda document: vector,
        __file__="qualified-native-test-double",
    ))


@pytest.mark.parametrize("system,machine", [
    ("linux", "x86_64"), ("linux", "aarch64"), ("win32", "amd64"),
    ("darwin", "x86_64"), ("darwin", "arm64"),
])
def test_bootstrap_wheel_matches_platform_requirement_and_qualified_bytes(installer, system, machine):
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = [Requirement(item) for item in config["project"]["dependencies"] if item.startswith("y-py")]
    environment = {"sys_platform": system, "platform_machine": "AMD64" if system == "win32" else machine}
    applicable = [requirement for requirement in requirements if requirement.marker.evaluate(environment)]
    filename, digest = installer.WHEELS[system, machine]
    assert len(applicable) == 1
    assert filename.split("-")[1] in applicable[0].specifier
    assert hashlib.sha256((ROOT / "vendor/y-py/wheels" / filename).read_bytes()).hexdigest() == digest
    records = json.loads((ROOT / "vendor/y-py/release-wheels.provenance.json").read_text(encoding="utf-8"))["wheels"]
    assert next(record for record in records if record["wheel"] == filename)["sha256"] == digest


@pytest.mark.parametrize("system,machine,normalized", [
    ("win32", "x86_64", "amd64"), ("win32", "AMD64", "amd64"),
    ("linux", "AMD64", "x86_64"), ("linux", "arm64", "aarch64"),
    ("darwin", "arm64", "arm64"),
])
def test_architecture_aliases_select_the_platform_wheel(installer, monkeypatch, system, machine, normalized):
    monkeypatch.setattr(installer, "sys", SimpleNamespace(platform=system))
    monkeypatch.setattr(installer.platform, "machine", lambda: machine)
    assert installer._selected_wheel() == installer.WHEELS[system, normalized]


def test_check_rejects_old_installed_version_without_installing(installer, monkeypatch):
    monkeypatch.setattr(installer.importlib.metadata, "version", lambda name: "0.6.2+adaos.1")
    monkeypatch.setattr(installer, "_selected_wheel", lambda: installer.WHEELS["win32", "amd64"])
    monkeypatch.setattr(installer.subprocess, "run", lambda *args, **kwargs: pytest.fail("--check installed a package"))
    with pytest.raises(RuntimeError, match=r"requires patched y-py==0.6.2\+adaos.2"):
        installer.main(["--check"])


def test_check_rejects_stale_native_binary_despite_new_metadata(installer, monkeypatch):
    monkeypatch.setattr(installer.importlib.metadata, "version", lambda name: "0.6.2+adaos.2")
    native_binary(monkeypatch, bytes.fromhex("02010f0201"))
    with pytest.raises(RuntimeError, match="clock integrity failed"):
        installer._verify_installation("0.6.2+adaos.2")


@pytest.mark.parametrize("vector", [bytes.fromhex("02010a0201"), bytes.fromhex("020201010a")])
def test_check_accepts_correct_native_clocks_in_either_order(installer, monkeypatch, vector):
    monkeypatch.setattr(installer.importlib.metadata, "version", lambda name: "0.6.2+adaos.2")
    native_binary(monkeypatch, vector)
    installer._verify_installation("0.6.2+adaos.2")


def test_arm_legacy_check_does_not_claim_gc_qualification(installer, monkeypatch, capsys):
    monkeypatch.setattr(installer.importlib.metadata, "version", lambda name: "0.6.2+adaos.1")
    native_binary(monkeypatch, bytes.fromhex("02010f0201"))
    installer._verify_installation("0.6.2+adaos.1")
    assert "GC fix is not qualified" in capsys.readouterr().out


@pytest.mark.parametrize("corrupt_cache", [False, True])
def test_bundled_qualified_wheel_is_available_without_release_download(installer, monkeypatch, tmp_path, corrupt_cache):
    filename, digest = installer.WHEELS["win32", "amd64"]
    if corrupt_cache:
        (tmp_path / filename).write_bytes(b"partial earlier download")
    monkeypatch.setattr(installer, "_download", lambda *args, **kwargs: pytest.fail("bundled wheel caused a download"))
    wheel = installer._prepare_wheel(tmp_path, filename=filename, expected_sha256=digest)
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == digest


def test_corrupt_bundled_wheel_is_rejected_before_installation(installer, monkeypatch, tmp_path):
    monkeypatch.setattr(installer, "__file__", str(tmp_path / "tools/install_patched_y_py.py"))
    filename, digest = installer.WHEELS["win32", "amd64"]
    bundled = tmp_path / "vendor/y-py/wheels" / filename
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"corrupt source artifact")
    destination = tmp_path / "cache"
    destination.mkdir()
    monkeypatch.setattr(installer, "_download", lambda *args, **kwargs: pytest.fail("corrupt bundled wheel was hidden by a download"))
    with pytest.raises(RuntimeError, match="bundled patched y-py wheel digest mismatch"):
        installer._prepare_wheel(destination, filename=filename, expected_sha256=digest)


@pytest.mark.parametrize("key,tag", [
    (("win32", "amd64"), "y-py-v0.6.2-adaos.2"),
    (("darwin", "arm64"), "y-py-v0.6.2-adaos.1"),
])
def test_download_uses_release_tag_of_selected_platform(installer, monkeypatch, tmp_path, key, tag):
    filename, _ = installer.WHEELS[key]
    payload = b"qualified test download"
    requests = []

    class Response(io.BytesIO):
        headers = {}

    def urlopen(request, timeout):
        requests.append(request.full_url)
        return Response(payload)

    monkeypatch.delenv("ADAOS_Y_PY_WHEEL_RELEASE_BASE", raising=False)
    monkeypatch.setattr(installer.urllib.request, "urlopen", urlopen)
    installer._download(tmp_path / filename, filename=filename, expected_sha256=hashlib.sha256(payload).hexdigest())
    assert requests == [f"https://github.com/inimatic/adaos/releases/download/{tag}/{urllib.parse.quote(filename, safe='._-')}"]


@pytest.mark.parametrize("engine", ["pip", "uv"])
def test_old_environment_is_upgraded_from_bundled_wheel_then_verified(installer, monkeypatch, tmp_path, engine):
    version = "0.6.2+adaos.1"
    calls = []

    def install(command, check):
        nonlocal version
        calls.append(command)
        assert command[-1].endswith("y_py-0.6.2+adaos.2-cp311-cp311-win_amd64.whl")
        assert Path(command[-1]).is_file()
        version = "0.6.2+adaos.2"

    monkeypatch.setattr(installer, "_selected_wheel", lambda: installer.WHEELS["win32", "amd64"])
    monkeypatch.setattr(installer.importlib.metadata, "version", lambda name: version)
    monkeypatch.setattr(installer.subprocess, "run", install)
    monkeypatch.setattr(installer.shutil, "which", lambda name: "uv")
    monkeypatch.setattr(installer, "_download", lambda *args, **kwargs: pytest.fail("upgrade required a download"))
    native_binary(monkeypatch)
    assert installer.main(["--installer", engine, "--wheelhouse", str(tmp_path)]) == 0
    assert len(calls) == 1


@pytest.mark.parametrize("relative", [
    "tools/bootstrap.sh", "tools/bootstrap_uv.sh", "tools/bootstrap.ps1", "tools/bootstrap_uv.ps1",
    "tools/bootstrap_distribution.py", "tools/qualify_bootstrap_release.py",
    "tools/build_bootstrap_release.py", "tools/windows_sandbox/check_runtime.py",
])
def test_every_bootstrap_acceptance_gate_uses_platform_native_verification(relative):
    source = (ROOT / relative).read_text(encoding="utf-8")
    assert "install_patched_y_py.py" in source
    assert "--check" in source
    assert "0.6.2+adaos.1" not in source
