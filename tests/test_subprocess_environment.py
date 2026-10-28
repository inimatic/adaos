from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from adaos.services.sandbox.service import _inherit_env_filtered
from adaos.services.skill_factory_worker import SubprocessCodexExecutor


@pytest.mark.parametrize("drive_key", ["SystemDrive", "SYSTEMDRIVE", "systemdrive"])
def test_sandbox_inherits_system_drive_without_arbitrary_secrets(drive_key: str) -> None:
    environment = _inherit_env_filtered(
        {drive_key: "C:", "SystemRoot": r"C:\Windows", "OPENAI_API_KEY": "fixture-secret"},
        inherit=True,
    )

    assert environment[drive_key] == "C:"
    assert environment["SystemRoot"] == r"C:\Windows"
    assert "OPENAI_API_KEY" not in environment


def test_sandbox_disabled_inheritance_still_inherits_nothing() -> None:
    assert _inherit_env_filtered({"SystemDrive": "C:", "PATH": "fixture"}, inherit=False) == {}


@pytest.mark.skipif(os.name != "nt", reason="Requires the Windows known-folder API")
@pytest.mark.parametrize("provider", ["sandbox", "builder"])
def test_filtered_child_resolves_real_windows_programdata(provider: str, tmp_path: Path) -> None:
    environment = (
        _inherit_env_filtered(None, inherit=True)
        if provider == "sandbox"
        else SubprocessCodexExecutor._bounded_environment()
    )
    # DONT_VERIFY reproduces the literal %SystemDrive%\ProgramData fallback
    # without creating folders or invoking a provider/network service.
    script = (
        "import ctypes, json\n"
        "buffer = ctypes.create_unicode_buffer(32768)\n"
        "status = ctypes.windll.shell32.SHGetFolderPathW(None, 0x4023, None, 0, buffer)\n"
        "print(json.dumps({'status': status, 'path': buffer.value}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    result = json.loads(completed.stdout)

    assert result["status"] == 0
    assert "%" not in result["path"], result
    assert Path(result["path"]).is_absolute(), result
    assert Path(result["path"]).is_dir(), result
    assert not (tmp_path / "%SystemDrive%").exists()
