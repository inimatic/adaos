from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path


def test_packaged_runtime_resolves_patched_y_py_release_wheels() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    requirements = [str(item) for item in project["dependencies"] if str(item).startswith("y-py")]
    wheel_index = (repo_root / "vendor" / "y-py" / "release-wheels.html").read_text(encoding="utf-8")
    wheel_dir = repo_root / "vendor" / "y-py" / "wheels"
    wheels = sorted(wheel_dir.glob("*.whl"))

    assert requirements == ["y-py==0.6.2+adaos.1"]
    assert wheel_index.count("/releases/download/y-py-v0.6.2-adaos.1/") == 4
    assert wheel_index.count("y_py-0.6.2%2Badaos.1-cp311-cp311-") == 4
    assert "manylinux_2_17_x86_64.manylinux2014_x86_64.whl" in wheel_index
    assert "win_amd64.whl" in wheel_index
    assert "macosx_11_0_arm64.whl" in wheel_index
    assert "macosx_10_15_x86_64.whl" in wheel_index
    assert wheel_index.count("#sha256=") == 4
    assert len(wheels) == 4
    for wheel in wheels:
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        assert f"{wheel.name.replace('+', '%2B')}#sha256={digest}" in wheel_index


def test_catalina_intel_uses_legacy_compatible_webrtc_stack() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    requirements = [str(item) for item in project["dependencies"] if str(item).startswith("aiortc")]

    assert requirements == [
        "aiortc>=1.9.0,<1.11.0 ; sys_platform == 'darwin' and platform_machine == 'x86_64'",
        "aiortc>=1.9.0 ; sys_platform != 'darwin' or platform_machine != 'x86_64'",
    ]

    cryptography_requirements = [
        str(item) for item in project["dependencies"] if str(item).startswith("cryptography")
    ]
    assert cryptography_requirements == [
        "cryptography>=42.0.0,<49.0.0 ; sys_platform == 'darwin' and platform_machine == 'x86_64'",
        "cryptography>=42.0.0 ; sys_platform != 'darwin' or platform_machine != 'x86_64'",
    ]


def test_repository_default_does_not_override_release_y_py_wheels() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    runtime_lock = (repo_root / "uv.lock").read_text(encoding="utf-8")

    assert "sources" not in config.get("tool", {}).get("uv", {})
    assert config["tool"]["uv"]["find-links"] == ["vendor/y-py/wheels"]
    assert 'source = { directory = "vendor/y-py" }' not in runtime_lock
    assert 'source = { registry = "vendor/y-py/wheels" }' in runtime_lock
    assert "y_py-0.6.2+adaos.1-cp311-cp311-manylinux_2_17_x86_64" in runtime_lock


def test_user_install_does_not_require_vosk_or_dev_dependencies() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    project = config["project"]

    assert not any(str(item).startswith("vosk") for item in project["dependencies"])
    assert project["optional-dependencies"]["offline-stt"] == ["vosk>=0.3.45"]

    for relative_path in ("tools/bootstrap.sh", "tools/bootstrap_uv.sh"):
        script = (repo_root / relative_path).read_text(encoding="utf-8")
        assert 'USER_INSTALL_SPEC="."' in script
        assert 'USER_INSTALL_SPEC=".[dev]"' in script

    for relative_path in ("tools/bootstrap.ps1", "tools/bootstrap_uv.ps1"):
        script = (repo_root / relative_path).read_text(encoding="utf-8")
        assert '$userInstallSpec = if ($Dev) { ".[dev]" } else { "." }' in script


def test_user_bootstraps_only_require_rust_for_explicit_vendored_builds() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    for relative_path in ("tools/bootstrap.sh", "tools/bootstrap_uv.sh"):
        script = (repo_root / relative_path).read_text(encoding="utf-8")
        assert "--build-vendored-y-py" in script
        assert script.index('if [[ "$BUILD_VENDORED_Y_PY" == "1" ]]') < script.index("command -v cargo")
        assert "--only-binary :all:" in script
        assert "--only-binary y-py" not in script

    uv_bash = (repo_root / "tools/bootstrap_uv.sh").read_text(encoding="utf-8")
    assert "uv pip install --python \"$ADAOS_PY\" --no-sources" in uv_bash
    assert "--only-binary :all:" in uv_bash

    for relative_path in ("tools/bootstrap.ps1", "tools/bootstrap_uv.ps1"):
        script = (repo_root / relative_path).read_text(encoding="utf-8")
        assert "BuildVendoredYPy" in script
        assert script.index("if ($BuildVendoredYPy)") < script.index("Get-Command cargo")
        assert "--only-binary :all:" in script
        assert "--only-binary y-py" not in script

    uv_powershell = (repo_root / "tools/bootstrap_uv.ps1").read_text(encoding="utf-8")
    assert "uv pip install --python $adaosPython --no-sources" in uv_powershell
    assert "--only-binary :all:" in uv_powershell


def test_wheel_workflow_builds_both_macos_architectures() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workflow = (repo_root / ".github" / "workflows" / "y-py-wheels.yml").read_text(encoding="utf-8")

    assert "os: macos-15\n" in workflow
    assert "machine: arm64" in workflow
    assert "os: macos-15-intel" in workflow
    assert "machine: x86_64" in workflow
    assert 'deployment_target: "11.0"' in workflow
    assert 'deployment_target: "10.15"' in workflow
    assert "macosx_10_15_x86_64.whl" in workflow
    assert 'MACOSX_DEPLOYMENT_TARGET=${{ matrix.deployment_target }}' in workflow
    assert "Verify macOS minimum deployment target" in workflow
