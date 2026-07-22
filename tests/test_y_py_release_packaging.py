from __future__ import annotations

import tomllib
from pathlib import Path


def test_packaged_runtime_resolves_patched_y_py_release_wheels() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    requirements = [str(item) for item in project["dependencies"] if str(item).startswith("y-py @ ")]

    assert len(requirements) == 3
    assert all("/releases/download/y-py-v0.6.2-adaos.1/" in item for item in requirements)
    assert all("y_py-0.6.2%2Badaos.1-cp311-cp311-" in item for item in requirements)
    assert any("sys_platform == 'linux' and platform_machine == 'x86_64'" in item for item in requirements)
    assert any("sys_platform == 'win32' and platform_machine == 'AMD64'" in item for item in requirements)
    assert any("sys_platform == 'darwin' and platform_machine == 'arm64'" in item for item in requirements)


def test_repository_development_keeps_vendored_y_py_override() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))

    assert config["tool"]["uv"]["sources"]["y-py"] == {"path": "vendor/y-py"}
