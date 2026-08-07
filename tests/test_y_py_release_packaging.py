from __future__ import annotations

import tomllib
from pathlib import Path


def test_packaged_runtime_resolves_patched_y_py_release_wheels() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    requirements = [str(item) for item in project["dependencies"] if str(item).startswith("y-py @ ")]

    assert len(requirements) == 4
    assert all("/releases/download/y-py-v0.6.2-adaos.1/" in item for item in requirements)
    assert all("y_py-0.6.2%2Badaos.1-cp311-cp311-" in item for item in requirements)
    assert any("sys_platform == 'linux' and platform_machine == 'x86_64'" in item for item in requirements)
    assert any("sys_platform == 'win32' and platform_machine == 'AMD64'" in item for item in requirements)
    assert any("sys_platform == 'darwin' and platform_machine == 'arm64'" in item for item in requirements)
    assert any("sys_platform == 'darwin' and platform_machine == 'x86_64'" in item for item in requirements)


def test_repository_development_keeps_vendored_y_py_override() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))

    assert config["tool"]["uv"]["sources"]["y-py"] == {"path": "vendor/y-py"}


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

    uv_bash = (repo_root / "tools/bootstrap_uv.sh").read_text(encoding="utf-8")
    assert "uv pip install --python \"$ADAOS_PY\" --no-sources" in uv_bash
    assert "--only-binary y-py" in uv_bash

    for relative_path in ("tools/bootstrap.ps1", "tools/bootstrap_uv.ps1"):
        script = (repo_root / relative_path).read_text(encoding="utf-8")
        assert "BuildVendoredYPy" in script
        assert script.index("if ($BuildVendoredYPy)") < script.index("Get-Command cargo")

    uv_powershell = (repo_root / "tools/bootstrap_uv.ps1").read_text(encoding="utf-8")
    assert "uv pip install --python $adaosPython --no-sources" in uv_powershell
    assert "--only-binary y-py" in uv_powershell


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
