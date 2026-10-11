from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.apps import adaosd_installation


REPO_ROOT = Path(__file__).resolve().parents[1]


def _node_config(base: Path) -> SimpleNamespace:
    certificates = base / "certificates"
    certificates.mkdir(parents=True, exist_ok=True)
    ca = certificates / "ca.pem"
    cert = certificates / "hub.pem"
    key = certificates / "hub.key"
    for path in (ca, cert, key):
        path.write_text(path.name, encoding="utf-8")
    return SimpleNamespace(
        role="hub",
        node_id="node-test",
        token="config-token",
        root_settings=SimpleNamespace(base_url="https://ru.api.inimatic.com"),
        ca_cert_path=lambda: ca,
        hub_cert_path=lambda: cert,
        hub_key_path=lambda: key,
    )


def test_generates_go_supervisor_contracts_and_preserves_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = tmp_path / "adaos"
    (base / "state").mkdir(parents=True)
    (base / "state" / "node_runtime.json").write_text(
        json.dumps({"token": "runtime-token"}),
        encoding="utf-8",
    )
    dotenv = tmp_path / ".env"
    dotenv.write_text("ADAOS_REALTIME_PORT=7422\n", encoding="utf-8")
    service_path = base / "config" / "adaosd" / "service.json"
    executable = Path(sys.executable).resolve()

    monkeypatch.setattr(adaosd_installation, "load_config", lambda: _node_config(base))
    monkeypatch.setattr(adaosd_installation, "realtime_sidecar_enabled", lambda *, role: role == "hub")
    monkeypatch.setattr(
        adaosd_installation,
        "resolve_realtime_remote_candidates",
        lambda: ["wss://ru.api.inimatic.com/nats", "wss://api.inimatic.com/nats"],
    )

    result = adaosd_installation.generate_configuration(
        base_dir=base,
        repo_root=REPO_ROOT,
        python_executable=executable,
        binary=executable,
        service_config_path=service_path,
        dotenv_path=dotenv,
        serve_host="127.0.0.1",
        serve_port=8777,
        control_port=8777,
        sidecar_implementation="go",
        approval_scope="m4c_canary",
        approval_id="approval-one",
        approval_stand="stand-one",
    )

    assert result["schema"] == "adaosd.bootstrap_configuration.v1"
    assert result["sidecar_managed"] is True
    service = json.loads(service_path.read_text(encoding="utf-8"))
    supervisor_path = Path(service["supervisor_config_path"])
    supervisor = json.loads(supervisor_path.read_text(encoding="utf-8"))
    assert [worker["startup_barrier"] for worker in supervisor["workers"][:1]] == [True]
    assert [Path(worker["profile_path"]).name for worker in supervisor["workers"]] == [
        "sidecar.json",
        "runtime.json",
    ]

    sidecar = json.loads((service_path.parent / "workers" / "sidecar.json").read_text(encoding="utf-8"))
    runtime = json.loads((service_path.parent / "workers" / "runtime.json").read_text(encoding="utf-8"))
    assert sidecar["command"][:2] == [str(executable), "realtime-sidecar"]
    assert sidecar["environment"]["set"]["ADAOS_REALTIME_REMOTE_WS_ALT"] == (
        "wss://api.inimatic.com/nats"
    )
    assert Path(runtime["working_directory"]).is_absolute()
    assert "{active_slot}" in runtime["working_directory"]

    private_env = base / "private" / "adaosd-supervisor.env"
    assert private_env.read_text(encoding="utf-8") == 'ADAOS_TOKEN="runtime-token"\n'
    if os.name != "nt":
        assert private_env.stat().st_mode & 0o777 == 0o600

    second = adaosd_installation.generate_configuration(
        base_dir=base,
        repo_root=REPO_ROOT,
        python_executable=executable,
        binary=executable,
        service_config_path=service_path,
        dotenv_path=dotenv,
        serve_host="127.0.0.1",
        serve_port=8777,
        control_port=8777,
        sidecar_implementation="python",
        approval_scope="m4c_canary",
        approval_id="approval-two",
        approval_stand="stand-two",
    )
    assert second["approval"]["id"] == "approval-one"
    python_sidecar = json.loads(
        (service_path.parent / "workers" / "sidecar.json").read_text(encoding="utf-8")
    )
    assert python_sidecar["command"][:3] == [str(executable), "-m", "adaos.services.realtime_sidecar"]
