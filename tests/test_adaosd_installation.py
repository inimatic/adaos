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
    monkeypatch.setenv("ADAOS_YJS_SIDECAR_SHADOW_ENABLE", "1")
    monkeypatch.setenv(
        "ADAOS_YJS_SIDECAR_SHADOW_WEBSPACES",
        "default,desktop,go-yjs-canary",
    )
    monkeypatch.setenv("ADAOS_YJS_SIDECAR_SHADOW_MAX_ROOMS", "3")
    monkeypatch.setenv("ADAOS_YJS_SIDECAR_SHADOW_MAX_SNAPSHOT_BYTES", "33554432")
    monkeypatch.setenv("ADAOS_YJS_SIDECAR_SHADOW_QUEUE_LIMIT", "64")
    monkeypatch.setenv("ADAOS_YJS_SIDECAR_AUTHORITY_WEBSPACES", "go-yjs-canary")
    monkeypatch.setenv("ADAOS_YJS_SIDECAR_AUTHORITY_MAX_MESSAGE_BYTES", "4194304")

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
    assert supervisor["update"]["implementation"] == "python_compat"
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
    for profile in (sidecar, runtime):
        assert profile["environment"]["set"]["ADAOS_YJS_SIDECAR_SHADOW_ENABLE"] == "1"
        assert profile["environment"]["set"]["ADAOS_YJS_SIDECAR_SHADOW_WEBSPACES"] == (
            "default,desktop,go-yjs-canary"
        )
        assert profile["environment"]["set"]["ADAOS_YJS_SIDECAR_SHADOW_MAX_ROOMS"] == "3"
        assert profile["environment"]["set"]["ADAOS_YJS_SIDECAR_SHADOW_MAX_SNAPSHOT_BYTES"] == (
            "33554432"
        )
        assert profile["environment"]["set"]["ADAOS_YJS_SIDECAR_SHADOW_QUEUE_LIMIT"] == "64"
        assert profile["environment"]["set"]["ADAOS_YJS_SIDECAR_SHADOW_PERSIST_ENABLE"] == "1"
        assert profile["environment"]["set"]["ADAOS_YJS_SIDECAR_SHADOW_STORAGE_DIR"] == str(
            base.resolve() / "state" / "yjs-shadow"
        )
        assert profile["environment"]["set"]["ADAOS_YJS_SIDECAR_SHADOW_CHECKPOINT_EVERY"] == "64"
        assert profile["environment"]["set"]["ADAOS_YJS_SIDECAR_AUTHORITY_WEBSPACES"] == (
            "go-yjs-canary"
        )
        assert profile["environment"]["set"]["ADAOS_YJS_SIDECAR_AUTHORITY_MAX_MESSAGE_BYTES"] == (
            "4194304"
        )
    assert Path(runtime["working_directory"]).is_absolute()
    assert "{active_slot}" in runtime["working_directory"]

    private_env = base / "private" / "adaosd-supervisor.env"
    assert private_env.read_text(encoding="utf-8") == 'ADAOS_TOKEN="runtime-token"\n'
    if os.name != "nt":
        assert private_env.stat().st_mode & 0o777 == 0o600

    monkeypatch.setenv("ADAOS_YJS_SIDECAR_SHADOW_ENABLE", "0")
    monkeypatch.delenv("ADAOS_YJS_SIDECAR_AUTHORITY_WEBSPACES")
    monkeypatch.delenv("ADAOS_YJS_SIDECAR_AUTHORITY_MAX_MESSAGE_BYTES")
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
        update_implementation="go",
        approval_scope="m4c_canary",
        approval_id="approval-two",
        approval_stand="stand-two",
    )
    assert second["approval"]["id"] == "approval-one"
    assert second["update_implementation"] == "go"
    updated_supervisor = json.loads(supervisor_path.read_text(encoding="utf-8"))
    assert updated_supervisor["update"] == {
        "implementation": "go",
        "prepare_timeout_s": 1800.0,
        "repo_url": "https://github.com/inimatic/adaos.git",
        "shared_dotenv_path": str(dotenv.resolve()),
        "source_archive_sha256": "",
        "source_archive_url": "",
        "source_archive_url_template": "",
        "source_mode": "auto",
    }
    python_sidecar = json.loads(
        (service_path.parent / "workers" / "sidecar.json").read_text(encoding="utf-8")
    )
    assert python_sidecar["command"][:3] == [str(executable), "-m", "adaos.services.realtime_sidecar"]
