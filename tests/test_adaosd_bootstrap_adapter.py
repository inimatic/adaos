from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTER = REPO_ROOT / "tools" / "adaosd_service_adapter.sh"
BOOTSTRAPS = (
    REPO_ROOT / "tools" / "bootstrap.sh",
    REPO_ROOT / "tools" / "bootstrap_uv.sh",
)


def _bash(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash is unavailable")
    try:
        probe = subprocess.run(
            [bash, "-c", "true"],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("bash is not usable")
    if probe.returncode != 0:
        pytest.skip("bash is not usable")
    return subprocess.run(
        [bash, "-c", script, "test", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_adapter_validates_explicit_linux_root_contract(tmp_path: Path) -> None:
    binary = tmp_path / "adaosd"
    binary.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    binary.chmod(0o755)
    config = tmp_path / "service.json"
    config.write_text("{}\n", encoding="utf-8")

    valid = _bash(
        'source "$1"; adaosd_service_adapter_validate go python "$2" "$3" always Linux 0 1',
        str(ADAPTER),
        str(binary),
        str(config),
    )
    assert valid.returncode == 0, valid.stderr

    non_root = _bash(
        'source "$1"; adaosd_service_adapter_validate go python "$2" "$3" always Linux 1000 1',
        str(ADAPTER),
        str(binary),
        str(config),
    )
    assert non_root.returncode == 2
    assert "requires root" in non_root.stderr

    no_service = _bash(
        'source "$1"; adaosd_service_adapter_validate go python "$2" "$3" never Linux 0 1',
        str(ADAPTER),
        str(binary),
        str(config),
    )
    assert no_service.returncode == 2
    assert "cannot be combined" in no_service.stderr


def test_adapter_allows_independent_python_supervisor_go_sidecar(tmp_path: Path) -> None:
    binary = tmp_path / "adaosd"
    binary.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    binary.chmod(0o755)

    valid = _bash(
        'source "$1"; adaosd_service_adapter_validate python go "$2" "" never Darwin 1000 0',
        str(ADAPTER),
        str(binary),
    )
    assert valid.returncode == 0, valid.stderr

    all_python = _bash(
        'source "$1"; adaosd_service_adapter_validate python python "" "" never Darwin 1000 0',
        str(ADAPTER),
    )
    assert all_python.returncode == 0, all_python.stderr


def test_adapter_defers_generated_service_config_requirement(tmp_path: Path) -> None:
    binary = tmp_path / "adaosd"
    binary.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    binary.chmod(0o755)

    early = _bash(
        'source "$1"; adaosd_service_adapter_validate go python "$2" "" always Linux 0 1',
        str(ADAPTER),
        str(binary),
    )
    assert early.returncode == 0, early.stderr

    required = _bash(
        'source "$1"; adaosd_service_adapter_require_service_config ""',
        str(ADAPTER),
    )
    assert required.returncode == 2
    assert "must be an absolute regular file" in required.stderr


def test_adapter_invokes_versioned_service_contract(tmp_path: Path) -> None:
    record = tmp_path / "args.json"
    binary = tmp_path / "adaosd"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "open(os.environ['ADAOSD_TEST_RECORD'], 'w', encoding='utf-8').write(json.dumps(sys.argv[1:]))\n"
        "print('{}')\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    config = tmp_path / "service.json"
    config.write_text("{}\n", encoding="utf-8")

    result = _bash(
        'source "$1"; ADAOSD_TEST_RECORD="$4" adaosd_service_adapter_install "$2" "$3"',
        str(ADAPTER),
        str(binary),
        str(config),
        str(record),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(record.read_text(encoding="utf-8")) == [
        "service",
        "install",
        "--config",
        str(config),
        "--replace",
        "--json",
    ]


def test_adapter_classifies_versioned_status_and_rejects_drift() -> None:
    managed = _bash(
        'source "$1"; printf %s "$2" | adaosd_service_adapter_classify_status "$3"',
        str(ADAPTER),
        json.dumps(
            {
                "schema": "adaosd.service_status.v1",
                "managed": True,
                "unit_matches": True,
            }
        ),
        shutil.which("python3") or "python3",
    )
    assert managed.returncode == 0, managed.stderr
    assert managed.stdout.strip() == "managed"

    drift = _bash(
        'source "$1"; printf %s "$2" | adaosd_service_adapter_classify_status "$3"',
        str(ADAPTER),
        json.dumps(
            {
                "schema": "adaosd.service_status.v1",
                "managed": True,
                "unit_matches": False,
            }
        ),
        shutil.which("python3") or "python3",
    )
    assert drift.returncode == 0, drift.stderr
    assert drift.stdout.strip() == "drift"

    unsupported = _bash(
        'source "$1"; printf %s "$2" | adaosd_service_adapter_classify_status "$3"',
        str(ADAPTER),
        json.dumps({"schema": "adaosd.service_status.v2"}),
        shutil.which("python3") or "python3",
    )
    assert unsupported.returncode != 0


def test_bootstraps_preserve_python_fallback_before_first_go_handoff() -> None:
    for bootstrap in BOOTSTRAPS:
        source = bootstrap.read_text(encoding="utf-8")
        assert 'source "${BOOTSTRAP_TOOL_DIR}/adaosd_service_adapter.sh"' in source
        assert "--supervisor-implementation" in source
        assert "--sidecar-implementation" in source
        assert "--runtime-engine" in source
        assert "--adaosd-binary" in source
        assert "--adaosd-service-config" in source
        assert "adaos.apps.adaosd_installation" in source
        assert 'ADAOS_SUPERVISOR_IMPLEMENTATION" "$SUPERVISOR_IMPLEMENTATION' in source
        assert 'ADAOS_SIDECAR_IMPLEMENTATION" "$SIDECAR_IMPLEMENTATION' in source
        assert "Managed adaosd service drift must be resolved before bootstrap" in source
        assert source.index("adaos.apps.adaosd_installation") < source.index(
            "adaosd_service_adapter_status"
        )
        assert source.index("adaos autostart enable") < source.index(
            'log "Handing systemd ownership to adaosd ..."'
        )
        assert "installer restored the Python fallback" in source


def test_uv_bootstrap_reuses_a_compatible_environment() -> None:
    source = (REPO_ROOT / "tools" / "bootstrap_uv.sh").read_text(encoding="utf-8")
    assert '[[ -x "$HOME/.local/bin/uv" ]]' in source
    assert '[[ -x "$ADAOS_PY" ]] && py_is_supported_python "$ADAOS_PY"' in source
    assert 'uv venv --clear --python "$UV_PYTHON" "$PWD/.venv"' in source


def test_bootstrap_and_adapter_are_valid_bash() -> None:
    for path in (ADAPTER, *BOOTSTRAPS):
        result = _bash('bash -n "$1"', str(path))
        assert result.returncode == 0, result.stderr
